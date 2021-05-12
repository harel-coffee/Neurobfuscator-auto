#Use to train iamgenet LSTM model
cd ../train_predictor

choice=obfuscator

normalize=smart

num_layers=1

num_hidden_list=(512 256 128)

dataset_type_list=("full")

num_epochs=50
for val1 in ${num_hidden_list[*]}; do
    for val2 in ${dataset_type_list[*]}; do
        num_hidden=$val1
        dataset_type=$val2
        model_name=deepsniffer_LSTM_both_autotvm_${normalize}_imagenet_${num_hidden}_depth

        if [[ "${dataset_type}" == "reduced" ]]; then
            model_path="./${choice}/predictor/logs_${model_name}"
            dataset="./${choice}/dataset/depth_train_data_dict.pickle"
        elif [[ "${dataset_type}" == "full" ]]; then
            model_path="./${choice}/predictor/logs_full_${model_name}"
            dataset="./${choice}/dataset/depth_train_data_dict_full.pickle"
        elif [[ "${dataset_type}" == "time_only" ]]; then
            model_path="./${choice}/predictor/logs_timeonly_${model_name}"
            dataset="./${choice}/dataset/depth_train_data_dict.pickle"
        fi

        python train.py --dataset ${dataset} --model_path ${model_path} --train_type ${dataset_type} --normalize ${normalize} --num_hidden ${num_hidden} --num_layers ${num_layers} --num_epochs ${num_epochs}

    done
done
